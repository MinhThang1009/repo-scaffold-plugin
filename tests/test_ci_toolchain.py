from __future__ import annotations

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, cast
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "ci_toolchain.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.ci_toolchain", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load ci_toolchain.py")
ci_toolchain = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ci_toolchain
SPEC.loader.exec_module(ci_toolchain)


def policy_document() -> dict[str, object]:
    return {
        "schema-version": 1,
        "documentation-python": "3.x",
        "tooling-python-minimum": "3.10",
        "npm-tools": {
            "markdownlint-cli2": {
                "package": "markdownlint-cli2",
                "version": "0.23.2",
            }
        },
        "standalone-tools": {
            "example": {
                "repository": "owner/example",
                "version": "1.2.3",
                "tag-template": "release-{version}",
                "asset-template": "example_{version}_linux.tar.gz",
                "archive-format": "tar.gz",
                "executable-path-template": "example-{version}/example",
                "sha256": "a" * 64,
            }
        },
    }


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class CiToolchainPolicyTests(unittest.TestCase):
    def write_policy(self, directory: str, document: object | None = None) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(
            json.dumps(policy_document() if document is None else document),
            encoding="utf-8",
        )
        return path

    def test_policy_emits_runtime_and_tool_outputs(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())

        self.assertEqual(
            ci_toolchain.github_output_lines(policy),
            [
                "documentation_python=3.x",
                "tooling_python_minimum=3.10",
                "markdownlint_cli2_package=markdownlint-cli2",
                "markdownlint_cli2_version=0.23.2",
                "example_repository=owner/example",
                "example_version=1.2.3",
                "example_tag=release-1.2.3",
                "example_asset=example_1.2.3_linux.tar.gz",
                "example_archive_format=tar.gz",
                "example_executable_path=example-1.2.3/example",
                f"example_sha256={'a' * 64}",
            ],
        )

    def test_policy_rejects_fixed_documentation_python(self) -> None:
        document = policy_document()
        document["documentation-python"] = "3.10"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "rolling stable CPython selector"
        ):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_tooling_python_minimum(self) -> None:
        document = policy_document()
        document["tooling-python-minimum"] = "latest"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "tooling-python-minimum"
        ):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_root_and_top_level_fields(self) -> None:
        cases = [
            ([], "policy root must be an object"),
            ({**policy_document(), "extra": True}, "unknown fields"),
            (
                {
                    key: value
                    for key, value in policy_document().items()
                    if key != "npm-tools"
                },
                "missing fields",
            ),
            ({**policy_document(), "schema-version": True}, "integer 1"),
            ({**policy_document(), "npm-tools": []}, "npm-tools must be an object"),
            (
                {**policy_document(), "standalone-tools": []},
                "standalone-tools must be an object",
            ),
        ]

        for document, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ci_toolchain.ToolchainError, message):
                    ci_toolchain.parse_policy(document)

    def test_npm_tool_rejects_invalid_names_shapes_fields_and_values(self) -> None:
        valid = {"package": "markdownlint-cli2", "version": "0.23.2"}
        cases = [
            ("Invalid", valid, "invalid npm tool name"),
            ("tool", [], "must be an object"),
            ("tool", {**valid, "extra": True}, "unknown fields"),
            ("tool", {"package": "tool"}, "missing fields"),
            ("tool", {**valid, "package": "bad package"}, "static npm name"),
            ("tool", {**valid, "version": "latest"}, "stable SemVer"),
        ]

        for name, value, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ci_toolchain.ToolchainError, message):
                    ci_toolchain.parse_npm_tool(name, value)

    def test_standalone_tool_rejects_invalid_names_shapes_fields_and_values(
        self,
    ) -> None:
        valid = cast(
            dict[str, object],
            cast(dict[str, object], policy_document()["standalone-tools"])["example"],
        )
        cases = [
            ("Invalid", valid, "invalid standalone tool name"),
            ("tool", [], "must be an object"),
            ("tool", {**valid, "extra": True}, "unknown fields"),
            (
                "tool",
                {key: value for key, value in valid.items() if key != "repository"},
                "missing fields",
            ),
            ("tool", {**valid, "repository": "invalid"}, "owner/repository"),
            ("tool", {**valid, "version": "latest"}, "stable SemVer"),
            ("tool", {**valid, "tag-template": "release"}, "tag-template"),
            ("tool", {**valid, "tag-template": "../{version}"}, "tag-template"),
            ("tool", {**valid, "archive-format": "zip"}, "archive-format"),
            (
                "tool",
                {**valid, "executable-path-template": 7},
                "executable-path-template",
            ),
        ]

        for name, value, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ci_toolchain.ToolchainError, message):
                    ci_toolchain.parse_tool(name, value)

    def test_standalone_tool_rejects_every_unsafe_executable_path_shape(self) -> None:
        valid = cast(
            dict[str, object],
            cast(dict[str, object], policy_document()["standalone-tools"])["example"],
        )
        unsafe_paths = [
            "{version}/{version}/tool",
            "{other}/tool",
            "tool}/binary",
            "directory\\tool",
            "/directory/tool",
            "directory//tool",
            "directory/./tool",
            "directory/../tool",
            "directory/-tool",
            "directory/tool name",
        ]

        for path in unsafe_paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "executable-path-template"
                ):
                    ci_toolchain.parse_tool(
                        "tool", {**valid, "executable-path-template": path}
                    )

    def test_policy_rejects_invalid_asset_template(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["asset-template"] = "../example.tar.gz"

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "asset-template"):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_invalid_digest(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["sha256"] = "not-a-digest"

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "64 lowercase hex"):
            ci_toolchain.parse_policy(document)

    def test_policy_rejects_unsafe_executable_path(self) -> None:
        document = policy_document()
        standalone_tools = cast(dict[str, object], document["standalone-tools"])
        tool = cast(dict[str, object], standalone_tools["example"])
        tool["executable-path-template"] = "../example"

        with self.assertRaisesRegex(
            ci_toolchain.ToolchainError, "executable-path-template"
        ):
            ci_toolchain.parse_policy(document)

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"schema-version":1,"schema-version":1}', encoding="utf-8")

            with self.assertRaisesRegex(
                ci_toolchain.ToolchainError, "duplicate JSON member"
            ):
                ci_toolchain.load_policy(path)

    def test_json_pair_loader_accepts_unique_members(self) -> None:
        self.assertEqual(
            ci_toolchain.reject_duplicate_json_pairs([("first", 1), ("second", 2)]),
            {"first": 1, "second": 2},
        )

    def test_policy_loader_wraps_file_decode_and_json_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_utf8 = root / "invalid-utf8.json"
            invalid_utf8.write_bytes(b"\xff")
            invalid_json = root / "invalid-json.json"
            invalid_json.write_text("{", encoding="utf-8")

            for path in (invalid_utf8, invalid_json, root / "missing.json"):
                with self.subTest(path=path.name):
                    with self.assertRaisesRegex(
                        ci_toolchain.ToolchainError, "could not read"
                    ):
                        ci_toolchain.load_policy(path)

    def test_latest_release_verifies_tag_asset_and_digest(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        tool = policy.tools[0]
        release = {
            "tag_name": "release-1.2.3",
            "assets": [
                {
                    "name": "example_1.2.3_linux.tar.gz",
                    "digest": f"sha256:{'a' * 64}",
                }
            ],
        }
        opener_calls: list[tuple[str, int]] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            opener_calls.append((request.full_url, timeout))
            return FakeResponse(json.dumps(release).encode())

        document = ci_toolchain.fetch_latest_release(tool, opener=opener)
        ci_toolchain.verify_latest_release(tool, document)

        self.assertEqual(
            opener_calls,
            [("https://api.github.com/repos/owner/example/releases/latest", 15)],
        )

    def test_latest_release_detects_version_and_digest_drift(self) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).tools[0]

        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "latest release"):
            ci_toolchain.verify_latest_release(
                tool, {"tag_name": "v1.2.4", "assets": []}
            )
        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "digest differs"):
            ci_toolchain.verify_latest_release(
                tool,
                {
                    "tag_name": "release-1.2.3",
                    "assets": [
                        {
                            "name": "example_1.2.3_linux.tar.gz",
                            "digest": f"sha256:{'b' * 64}",
                        }
                    ],
                },
            )

    def test_latest_release_rejects_invalid_documents_and_asset_cardinality(
        self,
    ) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).tools[0]
        cases = [
            ([], "must be an object"),
            ({"tag_name": tool.tag_name}, "has no asset list"),
            (
                {"tag_name": tool.tag_name, "assets": ["invalid"]},
                "exactly one",
            ),
            (
                {
                    "tag_name": tool.tag_name,
                    "assets": [
                        {"name": tool.asset_name},
                        {"name": tool.asset_name},
                    ],
                },
                "exactly one",
            ),
        ]

        for document, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ci_toolchain.ToolchainError, message):
                    ci_toolchain.verify_latest_release(tool, document)

    def test_latest_npm_release_uses_registry_and_detects_drift(self) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).npm_tools[0]
        opener_calls: list[tuple[str, int]] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            opener_calls.append((request.full_url, timeout))
            return FakeResponse(b'{"version":"0.23.2"}')

        document = ci_toolchain.fetch_latest_npm_tool(tool, opener=opener)
        ci_toolchain.verify_latest_npm_tool(tool, document)

        self.assertEqual(
            opener_calls,
            [("https://registry.npmjs.org/markdownlint-cli2/latest", 15)],
        )
        with self.assertRaisesRegex(ci_toolchain.ToolchainError, "latest npm"):
            ci_toolchain.verify_latest_npm_tool(tool, {"version": "0.23.3"})

    def test_latest_npm_release_rejects_documents_without_a_version(self) -> None:
        tool = ci_toolchain.parse_policy(policy_document()).npm_tools[0]

        documents: tuple[object, ...] = ([], {}, {"version": 7})
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "returned no version"
                ):
                    ci_toolchain.verify_latest_npm_tool(tool, document)

    def test_release_fetchers_bound_transport_size_and_json(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        release_tool = policy.tools[0]
        npm_tool = policy.npm_tools[0]

        for fetcher, tool in (
            (ci_toolchain.fetch_latest_release, release_tool),
            (ci_toolchain.fetch_latest_npm_tool, npm_tool),
        ):
            with self.subTest(fetcher=fetcher.__name__, case="transport"):

                def failing_opener(_request: object, *, timeout: int) -> FakeResponse:
                    self.assertEqual(timeout, 15)
                    raise OSError("offline")

                with self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "could not query"
                ):
                    fetcher(tool, opener=failing_opener)

            with self.subTest(fetcher=fetcher.__name__, case="size"):

                def oversized_opener(_request: object, *, timeout: int) -> FakeResponse:
                    self.assertEqual(timeout, 15)
                    return FakeResponse(b"x" * (ci_toolchain.MAX_RESPONSE_BYTES + 1))

                with self.assertRaisesRegex(ci_toolchain.ToolchainError, "too large"):
                    fetcher(tool, opener=oversized_opener)

            with self.subTest(fetcher=fetcher.__name__, case="json"):

                def invalid_json_opener(
                    _request: object, *, timeout: int
                ) -> FakeResponse:
                    self.assertEqual(timeout, 15)
                    return FakeResponse(b"{")

                with self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "invalid JSON"
                ):
                    fetcher(tool, opener=invalid_json_opener)

    def test_all_release_pins_are_verified(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        with (
            mock.patch.object(
                ci_toolchain,
                "fetch_latest_npm_tool",
                return_value={"version": "0.23.2"},
            ) as fetch_npm,
            mock.patch.object(ci_toolchain, "verify_latest_npm_tool") as verify_npm,
            mock.patch.object(
                ci_toolchain,
                "fetch_latest_release",
                return_value={"tag_name": "release-1.2.3", "assets": []},
            ) as fetch_release,
            mock.patch.object(ci_toolchain, "verify_latest_release") as verify_release,
        ):
            ci_toolchain.verify_latest_releases(policy)

        fetch_npm.assert_called_once_with(policy.npm_tools[0])
        verify_npm.assert_called_once_with(policy.npm_tools[0], {"version": "0.23.2"})
        fetch_release.assert_called_once_with(policy.tools[0])
        verify_release.assert_called_once_with(
            policy.tools[0], {"tag_name": "release-1.2.3", "assets": []}
        )

    def test_executable_resolution_accepts_only_external_absolute_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            external = root / "external"
            missing = root / "missing"
            forbidden.mkdir()
            external.mkdir()
            missing.mkdir()
            inside_tool = forbidden / "npx"
            outside_tool = external / "npx"
            inside_tool.touch()
            outside_tool.touch()
            path_value = os.pathsep.join(
                ["", "relative", str(missing), str(forbidden), str(external)]
            )

            def resolve_tool(_name: str, *, path: str) -> str | None:
                if path == str(forbidden):
                    return str(inside_tool)
                if path == str(external):
                    return str(outside_tool)
                return None

            with (
                mock.patch.dict(os.environ, {"PATH": path_value}),
                mock.patch.object(
                    ci_toolchain.shutil, "which", side_effect=resolve_tool
                ),
            ):
                result = ci_toolchain.resolve_path_executable(
                    "npx", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))

            with (
                mock.patch.dict(os.environ, {"PATH": "relative"}),
                mock.patch.object(ci_toolchain.shutil, "which") as which,
            ):
                self.assertIsNone(
                    ci_toolchain.resolve_path_executable(
                        "npx", forbidden_root=forbidden
                    )
                )
            which.assert_not_called()

    def test_executable_resolution_ignores_unresolvable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            broken_directory = root / "broken"
            external = root / "external"
            forbidden.mkdir()
            broken_directory.mkdir()
            external.mkdir()
            broken_tool = broken_directory / "npx"
            outside_tool = external / "npx"
            outside_tool.touch()
            original_resolve = Path.resolve

            def resolve_path(path: Path, strict: bool = False) -> Path:
                if path == broken_tool:
                    raise RuntimeError("unresolvable candidate")
                return original_resolve(path, strict=strict)

            def resolve_tool(_name: str, *, path: str) -> str:
                if path == str(broken_directory):
                    return str(broken_tool)
                return str(outside_tool)

            with (
                mock.patch.dict(
                    os.environ,
                    {"PATH": os.pathsep.join([str(broken_directory), str(external)])},
                ),
                mock.patch.object(
                    ci_toolchain.shutil, "which", side_effect=resolve_tool
                ),
                mock.patch.object(ci_toolchain.Path, "resolve", resolve_path),
            ):
                result = ci_toolchain.resolve_path_executable(
                    "npx", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))

    def test_markdownlint_command_consumes_the_policy_pin_without_a_shell(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                ci_toolchain, "resolve_path_executable", return_value="/tools/npx"
            ),
            mock.patch.object(
                ci_toolchain.subprocess, "run", return_value=completed
            ) as run,
        ):
            self.assertEqual(ci_toolchain.run_markdownlint(policy), 0)

        run.assert_called_once_with(
            [
                "/tools/npx",
                "--yes",
                "markdownlint-cli2@0.23.2",
                *ci_toolchain.MARKDOWNLINT_GLOBS,
            ],
            cwd=Path.cwd(),
            check=False,
            timeout=300,
        )

    def test_markdownlint_rejects_missing_policy_pin_or_executable(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        no_npm_tools = ci_toolchain.CiToolchainPolicy(
            documentation_python=policy.documentation_python,
            tooling_python_minimum=policy.tooling_python_minimum,
            npm_tools=(),
            tools=policy.tools,
        )
        duplicate_npm_tools = ci_toolchain.CiToolchainPolicy(
            documentation_python=policy.documentation_python,
            tooling_python_minimum=policy.tooling_python_minimum,
            npm_tools=(policy.npm_tools[0], policy.npm_tools[0]),
            tools=policy.tools,
        )

        for invalid_policy in (no_npm_tools, duplicate_npm_tools):
            with self.subTest(count=len(invalid_policy.npm_tools)):
                with self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "exactly one markdownlint"
                ):
                    ci_toolchain.run_markdownlint(invalid_policy)

        with (
            mock.patch.object(
                ci_toolchain, "resolve_path_executable", return_value=None
            ),
            self.assertRaisesRegex(ci_toolchain.ToolchainError, "npx is unavailable"),
        ):
            ci_toolchain.run_markdownlint(policy)

    def test_markdownlint_wraps_process_start_and_timeout_errors(self) -> None:
        policy = ci_toolchain.parse_policy(policy_document())
        for error in (
            OSError("could not start"),
            ci_toolchain.subprocess.TimeoutExpired(["npx"], 300),
        ):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(
                    ci_toolchain,
                    "resolve_path_executable",
                    return_value="/tools/npx",
                ),
                mock.patch.object(ci_toolchain.subprocess, "run", side_effect=error),
                self.assertRaisesRegex(
                    ci_toolchain.ToolchainError, "could not run markdownlint"
                ),
            ):
                ci_toolchain.run_markdownlint(policy)

    def test_main_covers_every_operation_and_error_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)

            validate_output = StringIO()
            with redirect_stdout(validate_output):
                self.assertEqual(
                    ci_toolchain.main(["--policy", str(path), "validate"]), 0
                )
            self.assertIn("1 npm tool(s)", validate_output.getvalue())

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    ci_toolchain.main(["--policy", str(path), "emit-github-output"]),
                    0,
                )
            self.assertIn("documentation_python=3.x", output.getvalue())

            output = StringIO()
            with (
                mock.patch.object(ci_toolchain, "verify_latest_releases") as verify,
                redirect_stdout(output),
            ):
                self.assertEqual(
                    ci_toolchain.main(
                        ["--policy", str(path), "verify-latest-releases"]
                    ),
                    0,
                )
            verify.assert_called_once()
            self.assertIn("match the latest", output.getvalue())

            with mock.patch.object(
                ci_toolchain, "run_markdownlint", return_value=9
            ) as markdownlint:
                self.assertEqual(
                    ci_toolchain.main(["--policy", str(path), "run-markdownlint"]),
                    9,
                )
            markdownlint.assert_called_once()

            error_output = StringIO()
            with redirect_stderr(error_output):
                self.assertEqual(
                    ci_toolchain.main(
                        [
                            "--policy",
                            str(Path(directory) / "missing.json"),
                            "validate",
                        ]
                    ),
                    1,
                )
            self.assertIn("error: could not read", error_output.getvalue())

    def test_script_entrypoint_returns_main_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            output = StringIO()
            argv = [str(SCRIPT_PATH), "--policy", str(path), "validate"]
            with (
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertIn("CI toolchain policy is valid", output.getvalue())


if __name__ == "__main__":
    unittest.main()
