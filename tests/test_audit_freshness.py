from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar
from unittest import mock
from urllib.request import Request


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
            freshness.PYPI_OPENER,
            "open",
            return_value=FakeResponse(b'{"ok": true}'),
        ) as open_url:
            self.assertEqual(
                freshness.read_json("https://example.test/data"), {"ok": True}
            )
        self.assertEqual(open_url.call_args.kwargs["timeout"], 30)
        for payload in (
            b"[1]",
            b"{",
            b'{"info":{"version":"1.0.0"},"info":{"version":"2.0.0"}}',
            b"x" * (freshness.MAX_RESPONSE_BYTES + 1),
        ):
            with self.subTest(payload_size=len(payload)):
                with mock.patch.object(
                    freshness.PYPI_OPENER, "open", return_value=FakeResponse(payload)
                ):
                    with self.assertRaises(freshness.AuditError):
                        freshness.read_json("https://example.test/data")
        with mock.patch.object(
            freshness.PYPI_OPENER, "open", side_effect=OSError("offline")
        ):
            with self.assertRaisesRegex(freshness.AuditError, "request failed"):
                freshness.read_json("https://example.test/data")

    def test_read_json_rejects_redirects_before_following_them(self) -> None:
        handler = freshness.RejectRedirectHandler()

        with self.assertRaisesRegex(freshness.AuditError, "redirects are not allowed"):
            handler.redirect_request(
                Request("https://pypi.org/pypi/example/json"),
                None,
                302,
                "Found",
                None,
                "https://example.test/redirected",
            )

    def test_pinned_requirements_rejects_invalid_empty_and_conflicting_pins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.in"
            path.write_text("ruff==1.0.0\n", encoding="utf-8")
            self.assertEqual(
                freshness.pinned_requirements(path), {"ruff": ("ruff", "1.0.0")}
            )
            path.write_text("ruff==1.0.0  # retained rationale\n", encoding="utf-8")
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

    def test_action_findings_accepts_project_actions_outside_sync_allowlist(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8")
                + "      - uses: actions/setup-node@"
                + "a" * 40
                + " # v1.0.0\n",
                encoding="utf-8",
            )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            calls: list[str] = []

            def lookup(repository: str) -> Any:
                calls.append(repository)
                return release("v2.0.0", "b" * 40)

            findings = freshness.action_findings(
                root, trackers.workflow_directories, lookup
            )
            self.assertEqual(calls, ["actions/checkout", "actions/setup-node"])
            self.assertTrue(
                any(finding["subject"] == "actions/setup-node" for finding in findings)
            )

    def test_action_findings_raises_lookup_error_without_an_error_sink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            with self.assertRaisesRegex(ValueError, "release unavailable"):
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: (_ for _ in ()).throw(
                        ValueError("release unavailable")
                    ),
                )

    def test_invalid_workflow_does_not_skip_other_action_pin_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            invalid = root / ".github/workflows/bad.yml"
            invalid.write_text(
                "jobs:\n  bad:\n    steps:\n      - uses: actions/setup-node@v1\n",
                encoding="utf-8",
            )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            with self.assertRaisesRegex(freshness.AuditError, "bad.yml"):
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v2.0.0", "b" * 40),
                )

            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v2.0.0", "b" * 40),
                "googleapis/release-please": release("v17.6.0", "c" * 40),
            }[repository]
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("bad.yml", report["errors"][0])
            self.assertEqual(
                {
                    item["path"]
                    for item in report["findings"]
                    if item["kind"] == "action-pin"
                },
                {
                    ".github/workflows/ci.yml",
                    "skills/repo-scaffold/assets/workflows/ci.yml",
                },
            )

    def test_invalid_workflow_directory_does_not_skip_other_action_reminders(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            asset_workflow = root / "skills/repo-scaffold/assets/workflows/ci.yml"
            asset_workflow.unlink()
            asset_workflow.parent.rmdir()
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            with self.assertRaisesRegex(freshness.AuditError, "workflow directory"):
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v2.0.0", "b" * 40),
                )

            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v2.0.0", "b" * 40),
                "googleapis/release-please": release("v17.6.0", "c" * 40),
            }[repository]
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertTrue(
                any("assets/workflows" in error for error in report["errors"])
            )
            self.assertEqual(
                [
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                    if item["kind"] == "action-pin"
                ],
                [("action-pin", ".github/workflows/ci.yml", "actions/checkout")],
            )

    def test_action_findings_accepts_equivalent_uppercase_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            for relative in (
                Path(".github/workflows/ci.yml"),
                Path("skills/repo-scaffold/assets/workflows/ci.yml"),
            ):
                workflow = root / relative
                workflow.write_text(
                    workflow.read_text(encoding="utf-8").replace("a" * 40, "A" * 40),
                    encoding="utf-8",
                )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)

            self.assertEqual(
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v1.0.0", "a" * 40),
                ),
                [],
            )

    def test_action_findings_normalizes_double_quoted_continued_references(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            for relative in (
                Path(".github/workflows/ci.yml"),
                Path("skills/repo-scaffold/assets/workflows/ci.yml"),
            ):
                workflow = root / relative
                workflow.write_text(
                    workflow.read_text(encoding="utf-8").replace(
                        "actions/checkout@" + "a" * 40,
                        '"actions/chec\\\n          kout@' + "a" * 40 + '"',
                    ),
                    encoding="utf-8",
                )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)

            self.assertEqual(
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v1.0.0", "a" * 40),
                ),
                [],
            )

    def test_action_findings_normalizes_double_quoted_slash_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            for relative in (
                Path(".github/workflows/ci.yml"),
                Path("skills/repo-scaffold/assets/workflows/ci.yml"),
            ):
                workflow = root / relative
                workflow.write_text(
                    workflow.read_text(encoding="utf-8").replace(
                        "actions/checkout@" + "a" * 40,
                        '"act\\u0069ons\\u002fcheck\\x6fut\\u0040'
                        + "a" * 39
                        + '\\x61"',
                    ),
                    encoding="utf-8",
                )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)

            self.assertEqual(
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v1.0.0", "a" * 40),
                ),
                [],
            )

    def test_action_findings_ignores_uses_text_in_run_block_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8")
                + "      - run: |\n"
                + "          uses: actions/checkout@"
                + "b" * 40
                + " # shell text\n"
                + '      - run: "echo started\n'
                + "          uses: actions/checkout@"
                + "d" * 40
                + " # shell text\n"
                + '          echo completed"\n',
                encoding="utf-8",
            )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)

            self.assertEqual(
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v1.0.0", "a" * 40),
                ),
                [],
            )

    def test_action_findings_uses_the_safe_yaml_aware_workflow_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            workflow = root / ".github/workflows/ci.yml"
            yaml_workflow = workflow.with_suffix(".yaml")
            workflow.rename(yaml_workflow)
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)

            findings = freshness.action_findings(
                root,
                trackers.workflow_directories,
                lambda _repository: release("v2.0.0", "b" * 40),
            )

            self.assertEqual(len(findings), 2)
            self.assertIn(
                ".github/workflows/ci.yaml",
                {finding["path"] for finding in findings},
            )
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "workflow_paths",
                    side_effect=ValueError("workflow file is unsafe: linked.yml"),
                ),
                self.assertRaisesRegex(freshness.AuditError, "workflow file is unsafe"),
            ):
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v2.0.0", "b" * 40),
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
                '{"$schema": "https://raw.githubusercontent.com/googleapis/'
                'release-please/v17.6.0/schemas/config.json", '
                '"$schema": "https://example.test/schema.json"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
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
            self.assertTrue(
                any(item["kind"] == "lock-consistency" for item in inconsistent)
            )

    def test_requirement_findings_reuses_latest_lookup_for_duplicate_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.in"
            second = root / "second.in"
            first.write_text("ruff==0.1.0\n", encoding="utf-8")
            second.write_text("ruff==0.1.0\n", encoding="utf-8")
            sources = (
                freshness.RequirementSource(first.relative_to(root), ()),
                freshness.RequirementSource(second.relative_to(root), ()),
            )
            calls: list[str] = []

            def latest_lookup(name: str) -> str:
                calls.append(name)
                return "0.1.0"

            self.assertEqual(
                freshness.requirement_findings(root, sources, latest_lookup), []
            )
            self.assertEqual(calls, ["ruff"])

    def test_requirement_findings_records_one_lookup_error_per_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("first.in", "second.in"):
                (root / filename).write_text("ruff==0.1.0\n", encoding="utf-8")
            sources = tuple(
                freshness.RequirementSource(Path(filename), ())
                for filename in ("first.in", "second.in")
            )
            errors: list[str] = []

            def unavailable(_name: str) -> str:
                raise freshness.AuditError("PyPI unavailable")

            self.assertEqual(
                freshness.requirement_findings(root, sources, unavailable, errors), []
            )
            self.assertEqual(errors, ["PyPI unavailable"])
            with self.assertRaisesRegex(freshness.AuditError, "PyPI unavailable"):
                freshness.requirement_findings(root, sources, unavailable)
            with self.assertRaisesRegex(freshness.AuditError, "requirements file"):
                freshness.requirement_findings(
                    root,
                    (freshness.RequirementSource(Path("missing.in"), ()),),
                    unavailable,
                )

    def test_optional_release_please_and_ci_toolchain_trackers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            document = json.loads(registry.read_text(encoding="utf-8"))
            document["release-please-configs"] = []
            document["optional-release-please-configs"] = ["release-please-config.json"]
            document["ci-toolchain-policies"] = [".github/ci-toolchain.json"]
            document["code-scanning-allowlists"] = []
            document["optional-code-scanning-allowlists"] = [
                ".github/code-scanning-allowlist.json"
            ]
            registry.write_text(json.dumps(document), encoding="utf-8")
            policy = root / ".github/ci-toolchain.json"
            policy.write_text("{}\n", encoding="utf-8")
            script = root / "scripts/ci_toolchain.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# bundled checker\n", encoding="utf-8")

            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            self.assertEqual(
                freshness.existing_optional_paths(
                    root, trackers.optional_release_please_configs
                ),
                (Path("release-please-config.json"),),
            )
            (root / "release-please-config.json").unlink()
            self.assertEqual(
                freshness.existing_optional_paths(
                    root, trackers.optional_release_please_configs
                ),
                (),
            )
            self.assertEqual(freshness.ci_toolchain_findings(root, ()), [])
            self.assertEqual(
                freshness.existing_optional_paths(
                    root, trackers.optional_code_scanning_allowlists
                ),
                (),
            )
            self.assertEqual(
                freshness.code_scanning_allowlist_findings(
                    root,
                    trackers.code_scanning_allowlists
                    + freshness.existing_optional_paths(
                        root, trackers.optional_code_scanning_allowlists
                    ),
                    date(2026, 9, 9),
                ),
                [],
            )
            (root / ".github/code-scanning-allowlist.json").write_text(
                json.dumps({"schema-version": 3, "allowlist": []}),
                encoding="utf-8",
            )
            self.assertEqual(
                freshness.existing_optional_paths(
                    root, trackers.optional_code_scanning_allowlists
                ),
                (Path(".github/code-scanning-allowlist.json"),),
            )

            current = mock.Mock(returncode=0, stderr="", stdout="current")
            with mock.patch.object(freshness.subprocess, "run", return_value=current):
                self.assertEqual(
                    freshness.ci_toolchain_findings(
                        root, trackers.ci_toolchain_policies
                    ),
                    [],
                )

            stale = mock.Mock(
                returncode=1,
                stderr=(
                    "error: markdownlint-cli2 policy pins 1.0.0, but latest npm "
                    "release is '2.0.0'; review the release and update the policy"
                ),
                stdout="",
            )
            with mock.patch.object(
                freshness.subprocess, "run", return_value=stale
            ) as run:
                findings = freshness.ci_toolchain_findings(
                    root, trackers.ci_toolchain_policies
                )
            self.assertEqual(findings[0]["kind"], "ci-toolchain")
            self.assertIn("verify-latest-releases", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 60)

            digest_drift = mock.Mock(
                returncode=1,
                stderr="error: actionlint asset digest differs from the reviewed policy",
                stdout="",
            )
            with mock.patch.object(
                freshness.subprocess, "run", return_value=digest_drift
            ):
                self.assertEqual(
                    freshness.ci_toolchain_findings(
                        root, trackers.ci_toolchain_policies
                    )[0]["kind"],
                    "ci-toolchain",
                )

            indeterminate = mock.Mock(
                returncode=1,
                stderr="error: could not query latest npm release",
                stdout="",
            )
            with (
                mock.patch.object(
                    freshness.subprocess, "run", return_value=indeterminate
                ),
                self.assertRaisesRegex(freshness.AuditError, "indeterminate"),
            ):
                freshness.ci_toolchain_findings(root, trackers.ci_toolchain_policies)
            with (
                mock.patch.object(
                    freshness.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("checker", 60),
                ),
                self.assertRaisesRegex(freshness.AuditError, "could not run"),
            ):
                freshness.ci_toolchain_findings(root, trackers.ci_toolchain_policies)

    def test_ci_toolchain_failure_does_not_skip_other_policy_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            document = json.loads(registry.read_text(encoding="utf-8"))
            document["ci-toolchain-policies"] = [
                ".github/first-toolchain.json",
                ".github/second-toolchain.json",
            ]
            registry.write_text(json.dumps(document), encoding="utf-8")
            for filename in ("first-toolchain.json", "second-toolchain.json"):
                (root / ".github" / filename).write_text("{}\n", encoding="utf-8")
            script = root / "scripts/ci_toolchain.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# bundled checker\n", encoding="utf-8")
            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v1.0.0", "a" * 40),
                "googleapis/release-please": release("v17.6.0", "b" * 40),
            }[repository]
            indeterminate = mock.Mock(
                returncode=1,
                stderr="error: could not query latest npm release",
                stdout="",
            )
            stale = mock.Mock(
                returncode=1,
                stderr="error: markdownlint-cli2 policy pins 1.0.0, but latest npm release is '2.0.0'",
                stdout="",
            )
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
                mock.patch.object(
                    freshness.subprocess, "run", side_effect=[indeterminate, stale]
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("first-toolchain.json", report["errors"][0])
            self.assertIn(
                ("ci-toolchain", ".github/second-toolchain.json"),
                {(item["kind"], item["path"]) for item in report["findings"]},
            )

            errors: list[str] = []
            with mock.patch.object(
                freshness.subprocess,
                "run",
                side_effect=[subprocess.TimeoutExpired("checker", 60), stale],
            ):
                findings = freshness.ci_toolchain_findings(
                    root,
                    (
                        Path(".github/first-toolchain.json"),
                        Path(".github/second-toolchain.json"),
                    ),
                    errors,
                )
            self.assertIn("could not run", errors[0])
            self.assertEqual(findings[0]["path"], ".github/second-toolchain.json")

            missing_policy = Path(".github/missing-toolchain.json")
            with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                freshness.ci_toolchain_findings(root, (missing_policy,))
            errors = []
            with mock.patch.object(freshness.subprocess, "run", return_value=stale):
                findings = freshness.ci_toolchain_findings(
                    root,
                    (missing_policy, Path(".github/second-toolchain.json")),
                    errors,
                )
            self.assertIn("missing or unsafe", errors[0])
            self.assertEqual(findings[0]["path"], ".github/second-toolchain.json")

    def test_code_scanning_allowlist_review_dates_and_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            allowlist = root / ".github/code-scanning-allowlist.json"
            allowlist.write_text(
                json.dumps(
                    {
                        "schema-version": 3,
                        "allowlist": [
                            {
                                "number": 7,
                                "tool": "CodeQL",
                                "rule": "py/example",
                                "path": "scripts/example.py",
                                "reason": "Reviewed exception.",
                                "reviewed-on": "2026-06-01",
                                "review-period-days": 90,
                            },
                            {
                                "number": 8,
                                "tool": "CodeQL",
                                "rule": "py/current",
                                "path": None,
                                "reason": "Recently reviewed exception.",
                                "reviewed-on": "2026-09-01",
                                "review-period-days": 90,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            findings = freshness.code_scanning_allowlist_findings(
                root,
                (Path(".github/code-scanning-allowlist.json"),),
                date(2026, 9, 9),
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["kind"], "code-scanning-allowlist-review")
            self.assertEqual(findings[0]["subject"], "alert #7: CodeQL/py/example")

            allowlist.write_text(
                json.dumps({"schema-version": 2, "allowlist": []}),
                encoding="utf-8",
            )
            legacy = freshness.code_scanning_allowlist_findings(
                root,
                (Path(".github/code-scanning-allowlist.json"),),
                date(2026, 9, 9),
            )
            self.assertEqual(legacy[0]["kind"], "code-scanning-allowlist-schema")

            allowlist.write_text(
                json.dumps({"schema-version": 3, "allowlist": [{"number": 1}]}),
                encoding="utf-8",
            )
            errors: list[str] = []
            self.assertEqual(
                freshness.code_scanning_allowlist_findings(
                    root,
                    (Path(".github/code-scanning-allowlist.json"),),
                    date(2026, 9, 9),
                    errors,
                ),
                [],
            )
            self.assertIn("review period", errors[0])

            valid = {
                "number": 1,
                "tool": "CodeQL",
                "rule": "py/example",
                "path": None,
                "reason": "Reviewed exception.",
                "reviewed-on": "2026-09-01",
                "review-period-days": 90,
            }
            invalid_documents: tuple[tuple[object, str], ...] = (
                ([], "must be an object"),
                ({"schema-version": 1, "allowlist": []}, "schema-version 3"),
                ({"schema-version": 3, "allowlist": {}}, "must be a list"),
                (
                    {"schema-version": 3, "allowlist": [{**valid, "number": True}]},
                    "invalid selector",
                ),
                (
                    {
                        "schema-version": 3,
                        "allowlist": [{**valid, "reviewed-on": "not-a-date"}],
                    },
                    "ISO date",
                ),
                (
                    {
                        "schema-version": 3,
                        "allowlist": [{**valid, "reviewed-on": "2999-01-01"}],
                    },
                    "future",
                ),
            )
            for document, message in invalid_documents:
                with self.subTest(document=document):
                    allowlist.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(freshness.AuditError, message):
                        freshness.code_scanning_allowlist_findings(
                            root,
                            (Path(".github/code-scanning-allowlist.json"),),
                            date(2026, 9, 9),
                        )

            allowlist.write_text(
                json.dumps({"schema-version": 3, "allowlist": [valid]}),
                encoding="utf-8",
            )
            with mock.patch.object(freshness, "MAX_CODE_SCANNING_ALLOWLIST_ENTRIES", 0):
                with self.assertRaisesRegex(freshness.AuditError, "entry limit"):
                    freshness.code_scanning_allowlist_findings(
                        root,
                        (Path(".github/code-scanning-allowlist.json"),),
                        date(2026, 9, 9),
                    )
            with mock.patch.object(freshness, "MAX_CODE_SCANNING_ALLOWLIST_BYTES", 1):
                with self.assertRaisesRegex(freshness.AuditError, "size limit"):
                    freshness.code_scanning_allowlist_findings(
                        root,
                        (Path(".github/code-scanning-allowlist.json"),),
                        date(2026, 9, 9),
                    )
            with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                freshness.code_scanning_allowlist_findings(
                    root,
                    (Path(".github/missing-code-scanning-allowlist.json"),),
                    date(2026, 9, 9),
                )

    def test_code_scanning_allowlist_audit_failure_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trackers = freshness.FreshnessTrackers(
                workflow_directories=(),
                release_please_configs=(),
                optional_release_please_configs=(),
                ci_toolchain_policies=(),
                code_scanning_allowlists=(
                    Path(".github/code-scanning-allowlist.json"),
                ),
                optional_code_scanning_allowlists=(),
                requirement_sources=(),
            )
            with (
                mock.patch.object(freshness, "load_trackers", return_value=trackers),
                mock.patch.object(
                    freshness,
                    "code_scanning_allowlist_findings",
                    side_effect=freshness.AuditError("allowlist unavailable"),
                ),
            ):
                report = freshness.audit(root, "token")
            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("allowlist unavailable", report["errors"])

    def test_tracker_registry_rejects_invalid_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            valid = json.loads(registry.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(freshness.AuditError, "non-empty"):
                freshness.safe_relative_path("", field="test")
            for value in (
                r"docs\README.md",
                "docs/./README.md",
                "docs//README.md",
                "C:/README.md",
                "docs/C:README.md",
            ):
                with (
                    self.subTest(value=value),
                    self.assertRaisesRegex(freshness.AuditError, "safe relative"),
                ):
                    freshness.safe_relative_path(value, field="test")
            with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                freshness.tracked_path(root, Path("missing"), kind="test path")
            with mock.patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                    freshness.tracked_path(
                        root, freshness.DEFAULT_TRACKER_REGISTRY, kind="test path"
                    )
            with mock.patch.object(
                freshness.sync_action_pins,
                "_path_has_link_or_reparse",
                return_value=True,
            ):
                with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                    freshness.tracked_path(
                        root, freshness.DEFAULT_TRACKER_REGISTRY, kind="test path"
                    )
            registry.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
                freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            registry.write_text(
                '{"schema-version": 1, "schema-version": 1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
                freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            registry.write_text(
                " " * (freshness.MAX_TRACKER_REGISTRY_BYTES + 1), encoding="utf-8"
            )
            with self.assertRaisesRegex(freshness.AuditError, "size limit"):
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
                (
                    {
                        **valid,
                        "workflow-directories": [".github/workflows"]
                        * (freshness.MAX_TRACKER_ENTRIES + 1),
                    },
                    "entry limit",
                ),
                ({**valid, "requirement-sources": "not-a-list"}, "sources"),
                (
                    {
                        **valid,
                        "requirement-sources": [valid["requirement-sources"][0]]
                        * (freshness.MAX_TRACKER_ENTRIES + 1),
                    },
                    "entry limit",
                ),
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
                mock.patch.object(
                    freshness,
                    "ci_toolchain_findings",
                    side_effect=freshness.AuditError("CI toolchain unavailable"),
                ),
            ):
                report = freshness.audit(root, "token")
            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("CI toolchain unavailable", report["errors"])

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
            report["findings"] = [
                {
                    "kind": "python|package\nnext",
                    "path": "requirements|dev.in\nnext",
                    "subject": "package|name\nnext",
                    "current": "1|0\nnext",
                    "latest": "2|0\nnext",
                    "details": "outdated",
                }
            ]
            self.assertIn(
                "| python\\|package next | `requirements\\|dev.in next` | "
                "`package\\|name next` | `1\\|0 next` | `2\\|0 next` |",
                freshness.markdown_report(report),
            )
            report["findings"] = []
            report["errors"] = ["offline"]
            report["status"] = "indeterminate"
            indeterminate_markdown = freshness.markdown_report(report)
            self.assertIn("## Indeterminate", indeterminate_markdown)
            self.assertNotIn(
                "No stale versioned inputs were found.", indeterminate_markdown
            )

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

    def test_action_failure_does_not_skip_independent_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8")
                + "      - uses: actions/setup-node@"
                + "a" * 40
                + " # v1.0.0\n",
                encoding="utf-8",
            )
            client = mock.Mock()

            def lookup(repository: str) -> Any:
                if repository == "actions/checkout":
                    raise ValueError("checkout release unavailable")
                if repository == "actions/setup-node":
                    return release("v2.0.0", "b" * 40)
                self.assertEqual(repository, "googleapis/release-please")
                return release("v17.7.0", "b" * 40)

            client.latest_release.side_effect = lookup
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
            ):
                report = freshness.audit(root, "synthetic-token")
            self.assertEqual(report["status"], "indeterminate")
            self.assertEqual(report["errors"], ["checkout release unavailable"])
            self.assertEqual(
                [
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                    if item["kind"] == "action-pin"
                ],
                [("action-pin", ".github/workflows/ci.yml", "actions/setup-node")],
            )
            self.assertEqual(
                {
                    item["path"]
                    for item in report["findings"]
                    if item["kind"] == "release-please-schema"
                },
                {
                    "release-please-config.json",
                    "skills/repo-scaffold/assets/release-please-config.json",
                    "skills/repo-scaffold/assets/release-please-config.vi.json",
                },
            )

    def test_invalid_release_please_config_does_not_skip_other_schema_reminders(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            (root / "release-please-config.json").write_text("{}\n", encoding="utf-8")
            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v1.0.0", "a" * 40),
                "googleapis/release-please": release("v17.7.0", "b" * 40),
            }[repository]
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("release-please-config.json", report["errors"][0])
            self.assertEqual(
                {
                    item["path"]
                    for item in report["findings"]
                    if item["kind"] == "release-please-schema"
                },
                {
                    "skills/repo-scaffold/assets/release-please-config.json",
                    "skills/repo-scaffold/assets/release-please-config.vi.json",
                },
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
            self.assertEqual(len(report["errors"]), 4)

            client = mock.Mock()
            client.latest_release.return_value = release("v17.6.0", "a" * 40)
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness,
                    "action_findings",
                    side_effect=freshness.AuditError("workflow input unavailable"),
                ),
                mock.patch.object(
                    freshness, "latest_pypi_release", return_value="1.0.0"
                ),
            ):
                report = freshness.audit(root, "synthetic-token")
            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("workflow input unavailable", report["errors"])

            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness,
                    "requirement_findings",
                    side_effect=freshness.AuditError(
                        "requirements checker unavailable"
                    ),
                ),
            ):
                report = freshness.audit(root, "synthetic-token")
            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("requirements checker unavailable", report["errors"])

        with (
            mock.patch.object(freshness, "main", return_value=0),
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    def test_pypi_failure_does_not_skip_other_packages_or_lock_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            (root / "requirements-dev.txt").write_text(
                "other==1.0.0\n", encoding="utf-8"
            )
            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v1.0.0", "a" * 40),
                "googleapis/release-please": release("v17.6.0", "b" * 40),
            }[repository]

            def latest(name: str) -> str:
                if name == "ruff":
                    raise freshness.AuditError("PyPI unavailable for ruff")
                return {"mutmut": "2.0.0", "markdown-it-py": "1.0.0"}[name]

            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(freshness, "latest_pypi_release", side_effect=latest),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertEqual(report["errors"], ["PyPI unavailable for ruff"])
            self.assertIn(
                ("lock-consistency", "requirements-dev.txt", "ruff"),
                {
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                },
            )
            self.assertIn(
                ("python-package", "requirements-mutation.in", "mutmut"),
                {
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                },
            )

    def test_requirement_input_error_does_not_skip_other_freshness_domains(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            (root / "requirements-dev.in").unlink()
            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v1.0.0", "a" * 40),
                "googleapis/release-please": release("v17.7.0", "b" * 40),
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
                        "mutmut": "2.0.0",
                        "markdown-it-py": "1.0.0",
                    }.__getitem__,
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertTrue(
                any("requirements file" in error for error in report["errors"])
            )
            self.assertIn(
                ("python-package", "requirements-mutation.in", "mutmut"),
                {
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                },
            )
            self.assertEqual(
                {
                    item["path"]
                    for item in report["findings"]
                    if item["kind"] == "release-please-schema"
                },
                {
                    "release-please-config.json",
                    "skills/repo-scaffold/assets/release-please-config.json",
                    "skills/repo-scaffold/assets/release-please-config.vi.json",
                },
            )

    def test_invalid_requirement_lock_does_not_skip_source_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            document = json.loads(registry.read_text(encoding="utf-8"))
            document["requirement-sources"][0]["locks"] = [
                "missing-lock.txt",
                "requirements-mutation.txt",
            ]
            registry.write_text(json.dumps(document), encoding="utf-8")
            (root / "requirements-mutation.txt").write_text(
                "other==1.0.0\n", encoding="utf-8"
            )
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            with self.assertRaisesRegex(freshness.AuditError, "requirements lock"):
                freshness.requirement_findings(
                    root,
                    trackers.requirement_sources,
                    lambda _name: "0.2.0",
                )

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
                        "ruff": "0.2.0",
                        "mutmut": "1.0.0",
                        "markdown-it-py": "1.0.0",
                    }.__getitem__,
                ),
            ):
                report = freshness.audit(root, "synthetic-token")

            self.assertEqual(report["status"], "indeterminate")
            self.assertTrue(
                any("missing-lock.txt" in error for error in report["errors"])
            )
            self.assertIn(
                ("python-package", "requirements-dev.in", "ruff"),
                {
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                },
            )
            self.assertIn(
                ("lock-consistency", "requirements-mutation.txt", "ruff"),
                {
                    (item["kind"], item["path"], item["subject"])
                    for item in report["findings"]
                },
            )

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


class AssetFreshnessTests(FreshnessTests):
    """Run freshness checks against the distributed script copy as well."""

    asset_module: ClassVar[ModuleType]
    asset_resolver: ClassVar[ModuleType]
    asset_scripts_directory = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts"
    asset_script_path = asset_scripts_directory / "audit_freshness.py"
    asset_sync_action_pins_path = asset_scripts_directory / "sync_action_pins.py"

    @classmethod
    def setUpClass(cls) -> None:
        resolver_specification = importlib.util.spec_from_file_location(
            "skills.repo-scaffold.scripts.sync_action_pins",
            cls.asset_sync_action_pins_path,
        )
        if resolver_specification is None or resolver_specification.loader is None:
            raise RuntimeError("Could not load asset sync_action_pins.py")
        cls.asset_resolver = importlib.util.module_from_spec(resolver_specification)
        sys.modules[resolver_specification.name] = cls.asset_resolver
        resolver_specification.loader.exec_module(cls.asset_resolver)

        original_resolver = sys.modules.get("sync_action_pins")
        sys.modules["sync_action_pins"] = cls.asset_resolver
        try:
            specification = importlib.util.spec_from_file_location(
                "skills.repo-scaffold.scripts.audit_freshness", cls.asset_script_path
            )
            if specification is None or specification.loader is None:
                raise RuntimeError("Could not load asset audit_freshness.py")
            cls.asset_module = importlib.util.module_from_spec(specification)
            sys.modules[specification.name] = cls.asset_module
            specification.loader.exec_module(cls.asset_module)
        finally:
            if original_resolver is None:
                del sys.modules["sync_action_pins"]
            else:
                sys.modules["sync_action_pins"] = original_resolver

    def setUp(self) -> None:
        global SCRIPT_PATH, freshness

        self.original_script_path = SCRIPT_PATH
        self.original_module = freshness
        self.original_resolver = sys.modules.get("sync_action_pins")
        SCRIPT_PATH = self.asset_script_path
        freshness = self.asset_module
        sys.modules["sync_action_pins"] = self.asset_resolver

    def tearDown(self) -> None:
        global SCRIPT_PATH, freshness

        SCRIPT_PATH = self.original_script_path
        freshness = self.original_module
        if self.original_resolver is None:
            del sys.modules["sync_action_pins"]
        else:
            sys.modules["sync_action_pins"] = self.original_resolver


if __name__ == "__main__":
    unittest.main()
